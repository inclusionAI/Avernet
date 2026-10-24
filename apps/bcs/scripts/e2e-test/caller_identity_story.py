#!/usr/bin/env python3
"""Caller identity end-to-end story against the scripted Provider mock.

Drives the guarded dispatch chain over live HTTP:
1. a Provider declares caller_capabilities (ensure routing) and a Bot opts in
   through the authoritative caller configuration subresource — including the
   Apply/Unchanged/Stale CAS outcomes;
2. a state_machine group created by the mock Human bootstraps its initial run
   (entry A) whose first node dispatches over the protected wire
   (caller.container.ensure + chat.send.protected carrying caller_context
   and an execution proof);
3. the Human answers a pending node over the session WebSocket (entry-B
   rearm) and the follow-up node rides the same protected wire;
4. the lease command surface is exercised on the state_machine group, and a
   plain caller group proves the lease fence, the web-send admission seam
   and the protected ride toward the caller-enabled worker — with the
   managed queue policy enabled and restored through its CAS API.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from group_session_ws_probe import _masked_close_frame, _read_upgrade_response


def segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def caller_definition() -> dict:
    """Linear state machine: bot draft -> human approval -> bot publish."""
    def bot_node(display: str) -> dict:
        return {
            "kind": "bot_task", "display_name": display,
            "assignee": {"type": "bot_binding", "binding": "worker"},
            "instruction": "Produce the caller-bound result the group asked for.",
        }
    return {
        "name": "E2E caller identity workflow",
        "participants": {"worker": {"display_name": "Caller worker", "required": True}},
        "runtime": {"kind": "state_machine", "state_machine": {
            "version": 2, "graph_mode": "hierarchical", "nodes": {
                "draft": {**bot_node("Draft"), "transitions": {"complete": {"targets": ["approve"]}}},
                "approve": {
                    "kind": "human_input", "display_name": "Approve",
                    "instruction": "Approve the caller-bound draft.",
                    "node_timeout_ms": 120000,
                    "transitions": {"complete": {"targets": ["publish"]}},
                },
                "publish": {**bot_node("Publish"), "final_output": True},
            },
        }},
    }


class WorkbenchSocket:
    """Minimal masked-frame WebSocket client for the group session channel."""

    def __init__(self, base: str, owner: str, group: str, session: str):
        url = urllib.parse.urlsplit(base)
        self.conn = socket.create_connection((url.hostname, url.port or 80), timeout=10)
        self.conn.settimeout(15)
        key = base64.b64encode(os.urandom(16)).decode()
        self.conn.sendall(
            ("GET /ws HTTP/1.1\r\n"
             f"Host: {url.netloc}\r\n"
             "Upgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
             f"X-Mock-User-Id: {owner}\r\n\r\n").encode())
        assert b"101 Switching Protocols" in _read_upgrade_response(self.conn)
        self.connect_id = "caller-ws-connect"
        self.send({"type": "req", "id": self.connect_id, "method": "connect",
                   "params": {"group_id": group, "session_id": session}})

    def send(self, payload: dict) -> None:
        frame = json.dumps(payload).encode()
        mask = os.urandom(4)
        length = len(frame)
        if length < 126:
            header = bytes([0x81, 0x80 | length])
        else:
            header = bytes([0x81, 0xFE]) + struct.pack("!H", length)
        self.conn.sendall(header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(frame)))

    def _read_exact(self, length: int) -> bytes:
        data = b""
        while len(data) < length:
            chunk = self.conn.recv(length - len(data))
            assert chunk, "Workbench closed the socket"
            data += chunk
        return data

    def receive(self, frame_id: str, attempts: int = 200) -> dict:
        for _ in range(attempts):
            opcode, size = self._read_exact(2)
            length = size & 127
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            payload = self._read_exact(length)
            if opcode & 15 != 1:
                continue
            message = json.loads(payload)
            if message.get("id") == frame_id:
                return message
            continue
        raise AssertionError(f"Workbench never answered frame {frame_id}")

    def close(self) -> None:
        self.conn.sendall(_masked_close_frame())
        self.conn.close()


class CallerStory:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.provider: dict = {}
        self.bot: dict = {}
        self.driver: dict = {}
        self.group_id = ""
        self.session_id = ""
        self.run_id = ""

    def request(self, path: str, body=None, *, method="GET", mock=False, headers=None, status=None):
        url = (self.args.mock_url if mock else self.args.base_url) + path
        auth = {} if mock else {"X-Mock-User-Id": self.args.human}
        request = urllib.request.Request(
            url, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **auth, **(headers or {})})
        try:
            response = self.http.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            code, raw = response.code, response.read().decode()
        if status is None:
            assert 200 <= code < 300, f"{method} {path}: HTTP {code}: {raw}"
        else:
            expected = code in status if isinstance(status, tuple) else code == status
            assert expected, f"{method} {path}: expected {status}, received {code}: {raw}"
        return json.loads(raw) if raw else None

    def post(self, path, body=None, **kwargs) -> dict:
        return self.request(path, {} if body is None else body, method="POST", **kwargs)

    def wait(self, description, read, predicate):
        deadline = time.monotonic() + 30
        value = None
        while time.monotonic() < deadline:
            value = read()
            if predicate(value):
                return value
            time.sleep(0.2)
        raise AssertionError(f"Timed out waiting for {description}")

    # ------------------------------------------------------------------ setup

    def setup(self) -> None:
        self.post("/control/reset", mock=True)
        self.post("/me/ensure-human")
        self.provider = self.post("/providers", {
            "name": "Caller E2E provider",
            "webhook_url": self.args.mock_url + "/provider/webhook",
            "auth": {"mode": "static_bearer"},
            "protocol_version": "2.0",
            "coordination": {"mode": "native_tool"},
            "caller_capabilities": {
                "container_routing": "ensure",
                "supports_caller": True,
                "supports_isolation_binding": True,
                "supports_observe_inject": True,
                "token_visibility": "runtime_only",
            },
        })
        provider_id = self.provider["provider_id"]
        self.bot = self.post(f"/providers/{segment(provider_id)}/bots", {
            "name": "Caller E2E worker", "summary": "Scripted caller-enabled provider bot",
            "owners": [self.args.human], "provider_bot_ref": "caller-e2e-" + uuid.uuid4().hex,
            "domains": ["caller"], "skills": ["dispatch"], "scopes": ["local"],
        }, headers={"Authorization": "Bearer " + self.provider["provider_admin_token"]})
        self.request(f"/bots/{segment(self.bot['bot_uuid'])}/visibility",
                     {"visibility": "public"}, method="PUT")
        if self.args.driver:
            self.driver = {"bot_uuid": self.args.driver}
        else:
            self.driver = self.post(f"/providers/{segment(provider_id)}/bots", {
                "name": "Caller E2E driver", "summary": "Scripted group driver bot",
                "owners": [self.args.human], "provider_bot_ref": "caller-e2e-driver-" + uuid.uuid4().hex,
            }, headers={"Authorization": "Bearer " + self.provider["provider_admin_token"]})
        self.configure_bot_caller(provider_id)

    def configure_bot_caller(self, provider_id: str) -> None:
        """Exercise the authoritative caller configuration subresource CAS."""
        path = f"/providers/{segment(provider_id)}/bots/{segment(self.bot['provider_bot_ref'])}/caller"
        headers = {"Authorization": "Bearer " + self.provider["provider_admin_token"]}
        fresh = self.request(path, headers=headers)
        assert fresh["snapshot"] is None, fresh
        snapshot = {"source_revision": 1, "caller_identity": {"enabled": True}}
        stored = {"source_revision": 1,
                  "caller_identity": {"enabled": True, "minimum_isolation": "caller_container"}}
        applied = self.request(path, {"snapshot": snapshot}, method="PATCH", headers=headers)
        assert applied["sync_status"] == "applied", applied
        readback = self.request(path, headers=headers)
        assert readback["snapshot"] == stored, readback
        # Same revision + same content: the storage CAS must report unchanged.
        unchanged = self.request(path, {"snapshot": snapshot}, method="PATCH", headers=headers)
        assert unchanged["sync_status"] == "unchanged", unchanged
        # Older revision: stale, never a blind overwrite.
        stale = self.request(path, {"snapshot": {"source_revision": 0,
            "caller_identity": {"enabled": True}}}, method="PATCH", headers=headers)
        assert stale["sync_status"] == "stale", stale
        print("  caller configuration subresource: Apply/Unchanged/Stale verified", flush=True)

    # ------------------------------------------------------------- protected

    def protected_dispatch(self, expected_content_hint: str) -> str:
        """Wait for and verify a protected chat.send ride, then answer it.

        The protected ride keeps the plain wire method name ("chat.send");
        protection rides the runtime-only extensions zone
        (caller_context / caller_execution_token / execution).
        """
        def is_protected(item):
            body = item["body"]
            return (body.get("method") == "chat.send"
                    and (body.get("extensions") or {}).get("caller_context"))
        requests = self.wait("protected dispatch", lambda: self.request("/control/provider/requests", mock=True)["requests"],
                             lambda items: any(is_protected(item) for item in items))
        protected = [item for item in requests if is_protected(item)]
        assert len(protected) == 1, "Unexpected concurrent protected deliveries"
        request = protected[0]
        assert request["authorization"] == "Bearer " + self.provider["bcs_to_provider_token"], request
        body = request["body"]
        extensions = body.get("extensions") or {}
        caller_context = extensions.get("caller_context") or {}
        assert caller_context.get("subject"), body
        assert (extensions.get("caller_execution_token") or {}).get("token"), body
        ensures = [item for item in requests if item["body"].get("method") == "caller.container.ensure"]
        assert ensures, "ensure routing must precede the protected ride"
        assert expected_content_hint in json.dumps(body.get("message") or {}), body
        self.post("/control/provider/clear", mock=True)
        return body["id"]

    def answer_dispatch(self, delivery_id: str, content: str) -> None:
        # The mock records chat.send before BCS consumed its HTTP ACK; the
        # callback intake may reject during transport negotiation — retry that
        # explicit rejection instead of treating it as a lost delivery.
        response = self.wait("provider callback transport negotiation", lambda: self.post(
            "/bot/events", {"run_id": delivery_id, "state": "final", "message": {"text": content}},
            headers={"X-BCN-Provider-Id": self.provider["provider_id"],
                     "Authorization": "Bearer " + self.bot["bot_runtime_token"]},
            status=(200, 409)), lambda body: body.get("error") != "transport_conflict")
        assert not response.get("error"), response

    # ------------------------------------------------------------------- run

    def bootstrap_group(self) -> None:
        group = self.post("/groups", {
            "driver_bot": self.driver["bot_uuid"], "label": "Caller E2E guarded dispatch",
            "participants": [{"bot_uuid": actor}
                             for actor in (self.driver["bot_uuid"], self.bot["bot_uuid"], "human_" + self.args.human)],
            "group_strategy": "state_machine",
            "collaboration_definition_yaml": json.dumps(caller_definition()),
            "participant_bindings": {"worker": {"source": "manual", "bot_ids": [self.bot["bot_uuid"]]}},
        })
        self.group_id = group["id"]
        self.session_id = group["session_id"]
        assert self.session_id, group

        def run_from_history():
            messages = self.request(f"/sessions/{segment(self.session_id)}/messages")
            run_ids = {((message.get("metadata") or {}).get("state_machine") or {}).get("run_id")
                       for message in messages}
            run_ids.discard(None)
            return next(iter(run_ids), None)

        self.run_id = self.wait("session history to publish the bootstrap run", run_from_history, bool)
        delivery = self.protected_dispatch("caller-bound result")
        self.answer_dispatch(delivery, "draft from the guarded caller ride")
        print("  bootstrap run dispatched over the protected wire (entry A)", flush=True)

    def pending(self) -> list[dict]:
        return self.wait("pending human node",
                         lambda: self.request(f"/state-machine-runs/{segment(self.run_id)}/pending-human-nodes"),
                         bool)

    def lease_roundtrip(self, socket_client: WorkbenchSocket) -> None:
        """Exercise the acquire/preempt/release surface plus its fences."""
        socket_client.send({"type": "req", "id": "lease-acquire", "method": "lease.acquire",
                            "params": {"groupId": self.group_id}})
        answer = socket_client.receive("lease-acquire")
        assert answer.get("ok") is True, answer
        assert answer["payload"]["status"] in ("granted", "self_held"), answer
        socket_client.send({"type": "req", "id": "lease-preempt", "method": "lease.preempt",
                            "params": {"groupId": self.group_id}})
        preempt = socket_client.receive("lease-preempt")
        assert preempt.get("ok") is True, preempt
        assert preempt["payload"]["status"] == "granted", preempt
        socket_client.send({"type": "req", "id": "lease-release", "method": "lease.release",
                            "params": {"groupId": self.group_id,
                                       "leaseId": preempt["payload"]["leaseId"],
                                       "leaseEpoch": preempt["payload"]["epoch"]}})
        release_answer = socket_client.receive("lease-release")
        assert release_answer.get("ok") is True, release_answer
        # Missing lease coordinates must be rejected as invalid_request.
        socket_client.send({"type": "req", "id": "lease-release-bare", "method": "lease.release",
                            "params": {"groupId": self.group_id}})
        bare = socket_client.receive("lease-release-bare")
        assert bare.get("ok") is False and bare["error"]["code"] == "invalid_request", bare
        assert "leaseId" in bare["error"]["message"], bare
        # A chat.send frame without sender material must not fan out as
        # anyone: the dispatcher rejects it with the closed forbidden error.
        socket_client.send({"type": "req", "id": "chat-anonymous", "method": "chat.send",
                            "params": {"group_id": self.group_id,
                                       "message": "must be rejected"}})
        anonymous = socket_client.receive("chat-anonymous")
        assert anonymous.get("ok") is False and anonymous["error"]["code"] == "forbidden_sender", anonymous

    def answer_approval_over_ws(self, socket_client: WorkbenchSocket) -> None:
        """Answer the pending HumanInput through the session WebSocket (entry B)."""
        nodes = self.pending()
        assert len(nodes) == 1, nodes
        socket_client.send({"type": "req", "id": "approve-send", "method": "chat.send",
                            "params": {"group_id": self.group_id,
                                       "session_id": self.session_id,
                                       "bot_id": "human_" + self.args.human,
                                       "message": "approve the guarded draft"}})
        answer = socket_client.receive("approve-send")
        assert answer.get("ok") is True, answer
        assert answer["payload"]["status"] == "accepted", answer
        delivery = self.protected_dispatch("the group asked for")
        self.answer_dispatch(delivery, "published caller output")
        self.wait("completed run", lambda: self.request(f"/state-machine-runs/{segment(self.run_id)}"),
                  lambda run: run["run"]["status"] == "completed")
        run = self.request(f"/state-machine-runs/{segment(self.run_id)}")
        assert run["run"]["output"] == "published caller output", run["run"]
        print("  human approval re-armed the association and published (entry B via WS)", flush=True)

    def state_machine_workbench(self) -> None:
        socket_client = WorkbenchSocket(self.args.base_url, self.args.human,
                                        self.group_id, self.session_id)
        try:
            connect = socket_client.receive(socket_client.connect_id)
            assert connect.get("ok") is True, connect
            self.answer_approval_over_ws(socket_client)
            self.lease_roundtrip(socket_client)
        finally:
            socket_client.close()
        print("  workbench lease/speaking checks verified over the session WebSocket", flush=True)

    # -------------------------------------------------------------------- ws

    def caller_group_chat(self) -> None:
        """Normal caller group: lease fence, admission, and the delegated ride."""
        group = self.post("/groups", {
            "driver_bot": self.driver["bot_uuid"], "label": "Caller E2E plain group",
            "participants": [{"bot_uuid": actor}
                             for actor in (self.driver["bot_uuid"], self.bot["bot_uuid"], "human_" + self.args.human)],
        })
        plain_group_id, plain_session_id = group["id"], group["session_id"]
        assert plain_session_id, group
        self.enable_group_queue_policy()
        self.post("/control/provider/clear", mock=True)
        socket_client = WorkbenchSocket(self.args.base_url, self.args.human,
                                        plain_group_id, plain_session_id)
        try:
            connect = socket_client.receive(socket_client.connect_id)
            assert connect.get("ok") is True, connect
            socket_client.send({"type": "req", "id": "lease-plain-acquire", "method": "lease.acquire",
                                "params": {"groupId": plain_group_id}})
            acquired = socket_client.receive("lease-plain-acquire")
            assert acquired.get("ok") is True, acquired
            lease = acquired["payload"]
            assert lease["status"] in ("granted", "self_held"), lease
            # A lease-required group refuses submissions without a lease: the web send
            # rejection propagates without a response frame (the dispatcher
            # fails the whole frame), so the client never receives an ack for
            # the fenced attempt. The refusal is verified by the ride audit
            # below: this turn must NOT deliver anything to the Provider.
            socket_client.send({"type": "req", "id": "chat-fence", "method": "chat.send",
                                "params": {"group_id": plain_group_id,
                                           "session_id": plain_session_id,
                                           "bot_id": "human_" + self.args.human,
                                           "message": "must be fenced"}})
            time.sleep(1.0)
            fenced_rides = self.request("/control/provider/requests", mock=True)["requests"]
            assert not any("must be fenced" in json.dumps(item["body"], ensure_ascii=False)
                           for item in fenced_rides), fenced_rides
            # The admitted turn carries the lease coordinates and rides the
            # caller admission path toward the caller-enabled target.
            socket_client.send({"type": "req", "id": "chat-plain-send", "method": "chat.send",
                                "params": {"group_id": plain_group_id,
                                           "bot_id": "human_" + self.args.human,
                                           "message": "route this caller turn to the worker",
                                           "session_id": plain_session_id,
                                           "mentions": [self.bot["bot_uuid"]],
                                           "leaseId": lease["leaseId"],
                                           "leaseEpoch": lease["epoch"]}})
            admitted = socket_client.receive("chat-plain-send")
            assert admitted.get("ok") is True, admitted
            # The admitted turn runs the caller admission seam and rides the
            # protected wire toward the caller-enabled worker: the Provider
            # must see the container ensure first, then chat.send with the
            # runtime-only caller_context and a real execution proof.
            def is_protected_ride(item):
                body = item["body"]
                return (body.get("method") == "chat.send"
                        and (body.get("extensions") or {}).get("caller_context"))
            rides = self.wait("protected admission ride",
                              lambda: self.request("/control/provider/requests", mock=True)["requests"],
                              lambda items: any(item["body"].get("method") == "caller.container.ensure"
                                                and any(is_protected_ride(item)
                                                        and "route this caller turn" in json.dumps(item["body"])
                                                        for item in items)
                                                for item in items))
            ride = next(item for item in rides
                        if is_protected_ride(item)
                        and "route this caller turn" in json.dumps(item["body"]))
            token = ride["body"]["extensions"]["caller_execution_token"]
            assert token["format"] == "jwt" and token["token"], token
            self.post("/control/provider/clear", mock=True)
            self.answer_dispatch(ride["body"]["id"], "worker acknowledges the caller turn")
            history = self.wait("human turn in session history",
                                lambda: self.request(f"/sessions/{segment(plain_session_id)}/messages"),
                                lambda messages: any("route this caller turn" in json.dumps(message)
                                                     for message in messages))
            assert history, history
            socket_client.send({"type": "req", "id": "lease-plain-release", "method": "lease.release",
                                "params": {"groupId": plain_group_id,
                                           "leaseId": lease["leaseId"],
                                           "leaseEpoch": lease["epoch"]}})
            released = socket_client.receive("lease-plain-release")
            released_ok = released.get("ok") is True
            # A stale epoch is a legitimate release rejection (preempted by
            # renewal during the turn); both outcomes are closed lease facts.
            assert released_ok or ("lease" in released["error"]["message"]), released
        finally:
            socket_client.close()
            self.restore_delivery_policy()
        print("  plain caller group: lease fence, admission and the protected ride verified", flush=True)

    def enable_group_queue_policy(self) -> None:
        """Queue admission (and the caller seam mounted on it) only runs for
        bots the managed delivery policy enforces; enable it for the story
        scope and restore the original policy afterwards."""
        current = self.request("/admin/message-delivery/policy")
        self._delivery_policy_restore = {"version": current["version"],
                                         "policy": current["policy"]}
        enforced = json.loads(json.dumps(current["policy"]))
        enforced["flow_enabled"]["group"] = True
        enforced["flow_enabled"]["system"] = True
        enforced["defaults"]["mode"] = "enforce"
        body = {"expected_version": current["version"], "policy": enforced}
        updated = self.request("/admin/message-delivery/policy", body, method="PUT")
        assert updated["version"] == current["version"] + 1, updated

    def restore_delivery_policy(self) -> None:
        restore = getattr(self, "_delivery_policy_restore", None)
        if not restore:
            return
        try:
            self.request("/admin/message-delivery/policy",
                         {"expected_version": restore["version"] + 1,
                          "policy": restore["policy"]}, method="PUT")
        except AssertionError:
            # Best-effort cleanup: a failed restore surfaces in the next
            # policy CAS rather than hiding inside this story.
            pass
        self._delivery_policy_restore = None

    def run(self) -> None:
        self.setup()
        self.bootstrap_group()
        self.state_machine_workbench()
        self.caller_group_chat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mock-url", required=True)
    parser.add_argument("--human", required=True, help="mock user id acting as the Human caller")
    parser.add_argument("--driver", default=None,
                        help="driver bot uuid of the group; a scripted driver bot is registered when omitted")
    args = parser.parse_args()
    CallerStory(args).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())