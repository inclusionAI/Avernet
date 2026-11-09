#!/usr/bin/env python3
"""Exercise A2A registration, Card management and delivery through a live BCS.

The local peer speaks public A2A HTTP+JSON/SSE. No model, Harness dependency,
direct database writes or test-only calls into BCS domain code are used.
"""
from __future__ import annotations

import argparse
import copy
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit, urlencode, quote

from provider_scope_story import Story
from a2a_workbench import abort_task
from a2a_management_cases import exercise as exercise_management
from a2a_rpc_peer import dispatch as dispatch_rpc, envelope as rpc_envelope

TOOLS = "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/tools/v1.md"


class Peer:
    def __init__(self, binding="HTTP+JSON"):
        self.binding = binding
        self.rpc_methods = set()
        self.bearer = ""
        self.requests = []
        self.tasks = {}
        self.poll_queries = {}
        self.observed_task_queries = set()
        self.methods = []
        self.cancels = []
        self.stops = {}
        self.card_reads = 0
        self.legacy_requests = 0
        self.mode = "stream"
        self.release_terminal = threading.Event()
        self.lock = threading.Lock()
        peer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass  # HTTP credentials and protocol messages are never logged.

            def json_response(self, status, body):
                encoded = json.dumps(rpc_envelope(self, body), ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def authenticated(self):
                valid = self.headers.get("Authorization") is None
                valid = valid and self.headers.get("A2A-Version") == "1.0"
                if not valid:
                    self.json_response(401, {"error": {"message": "Authentication required"}})
                return valid

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/agent-card.json":
                    with peer.lock:
                        peer.card_reads += 1
                        card = copy.deepcopy(peer.card)
                    self.json_response(200, card)
                elif path.startswith("/a2a/tasks/") and self.authenticated():
                    with peer.lock:
                        task_id = unquote(path.rsplit("/", 1)[-1])
                        peer.observed_task_queries.add(task_id)
                        if task_id in peer.poll_queries:
                            peer.poll_queries[task_id] += 1
                            if peer.poll_queries[task_id] >= 2:
                                peer.tasks[task_id]["status"]["state"] = "TASK_STATE_COMPLETED"
                        task = copy.deepcopy(peer.tasks.get(task_id))
                    self.json_response(200 if task else 404, task or {})
                else:
                    self.json_response(404, {})

            def do_POST(self):
                if dispatch_rpc(self, peer):
                    return
                path = urlsplit(self.path).path
                if path.startswith("/a2a/tasks/") and path.endswith(":cancel"):
                    if not self.authenticated():
                        return
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    task_id = unquote(path.rsplit("/", 1)[-1][:-7])
                    with peer.lock:
                        task = peer.tasks[task_id]
                        task["status"]["state"] = "TASK_STATE_CANCELED"
                        peer.cancels.append(task_id)
                        stopped = peer.stops.get(task_id)
                        response = copy.deepcopy(task)
                    if stopped:
                        stopped.set()
                    self.json_response(200, response)
                    return
                if path not in ("/a2a/message:stream", "/a2a/message:send"):
                    with peer.lock:
                        peer.legacy_requests += 1
                    self.json_response(404, {})
                    return
                if not self.authenticated():
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with peer.lock:
                    # Group/session lifecycle notifications also use the real A2A link.
                    # Acknowledge them separately from the human request assertions.
                    question = any("A2A question " in part.get("text", "")
                                   for part in body["message"].get("parts", []))
                    if question:
                        peer.requests.append(body)
                        peer.methods.append(path)
                    sequence = len(peer.requests)
                    context = body["message"].get("contextId") or "peer-context"
                    task_id = "peer-task-" + str(sequence)
                    text = "A2A-ANSWER-" + str(sequence)
                    task = {"id": task_id, "contextId": context,
                            "status": {"state": "TASK_STATE_COMPLETED"},
                            "artifacts": [{"artifactId": "answer", "parts": [{"text": text}]}]}
                    peer.tasks[task_id] = task
                    mode = peer.mode if question else "message"
                    if mode == "queued":
                        task["status"]["state"] = "TASK_STATE_WORKING"
                    if mode in ("wait", "replacement"):
                        task["status"]["state"] = "TASK_STATE_WORKING"
                        task["artifacts"] = []
                    if mode == "wait":
                        peer.stops[task_id] = threading.Event()
                    if not question:
                        text = "A2A setup acknowledged"
                if path == "/a2a/message:send":
                    assert body["configuration"]["returnImmediately"] is True
                    assert not self.headers.get("A2A-Extensions"), "Polling must not negotiate tools"
                    if mode == "poll_message" or not question:
                        self.json_response(200, {"message": {"messageId": "poll-reply-" + str(sequence),
                            "role": "ROLE_AGENT", "parts": [{"text": text}]}})
                    else:
                        with peer.lock:
                            task["status"]["state"] = "TASK_STATE_WORKING"
                            if mode == "poll_task":
                                peer.poll_queries[task_id] = 0
                            response = copy.deepcopy(task)
                        self.json_response(200, {"task": response})
                    return
                initial = {"task": {"id": task_id, "contextId": context,
                                    "status": {"state": "TASK_STATE_SUBMITTED"}}}
                events = [initial]
                if mode == "message":
                    events = [{"message": {"messageId": "peer-message-" + str(sequence),
                               "role": "ROLE_AGENT", "parts": [{"text": text}]}}]
                    if question:
                        events[0]["message"]["contextId"] = context
                elif mode in ("stream", "tools", "replacement", "queued"):
                    events += [
                        {"artifactUpdate": {"taskId": task_id, "contextId": context,
                         "artifact": {"artifactId": "answer", "parts": [{"text": "A2A-ANSWER-"}]},
                         "append": False}},
                        {"artifactUpdate": {"taskId": task_id, "contextId": context,
                         "artifact": {"artifactId": "answer", "parts": [{"text": str(sequence)}]},
                         "append": True, "lastChunk": True}},
                        {"statusUpdate": {"taskId": task_id, "contextId": context,
                         "status": {"state": "TASK_STATE_COMPLETED"}}},
                    ]
                if mode == "tools":
                    assert TOOLS in self.headers.get("A2A-Extensions", "")
                    records = [
                        {"phase": "start", "toolCallId": "e2e-call-" + str(sequence),
                         "name": "query_order", "args": {"id": "e2e-order"}},
                        {"phase": "result", "toolCallId": "e2e-call-" + str(sequence),
                         "name": "query_order", "isError": False,
                         "result": {"content": [{"type": "text", "text": "fixture-tool-result"}]}},
                    ]
                    observations = []
                    for record in records:
                        event = {"statusUpdate": {"taskId": task_id, "contextId": context,
                            "status": {"state": "TASK_STATE_WORKING", "message": {
                            "messageId": "observation-" + record["phase"], "role": "ROLE_AGENT",
                            "metadata": {TOOLS: {"version": "1"}}, "parts": [{"data": record}]}}}}
                        observations.extend([event, copy.deepcopy(event)])
                    events[2:2] = observations
                elif mode == "wait":
                    events.append({"artifactUpdate": {"taskId": task_id, "contextId": context,
                        "artifact": {"artifactId": "answer", "parts": [{"text": "A2A-WAITING-" + str(sequence)}]},
                        "append": False}})
                    # Complete a tool while the Task remains active; its text segment becomes visible.
                    events.append({"statusUpdate": {"taskId": task_id, "contextId": context,
                        "status": {"state": "TASK_STATE_WORKING", "message": {
                        "messageId": "waiting-tool", "role": "ROLE_AGENT",
                        "metadata": {TOOLS: {"version": "1"}}, "parts": [{"data": {
                        "phase": "start", "toolCallId": "cancel-call", "name": "slow_tool", "args": {}}}]}}}})
                    events.append({"statusUpdate": {"taskId": task_id, "contextId": context,
                        "status": {"state": "TASK_STATE_WORKING", "message": {
                        "messageId": "waiting-result", "role": "ROLE_AGENT",
                        "metadata": {TOOLS: {"version": "1"}}, "parts": [{"data": {
                        "phase": "result", "toolCallId": "cancel-call", "name": "slow_tool",
                        "isError": False, "result": {"content": [{"type": "text", "text": "tool finished"}]}}}]}}}})
                elif mode == "replacement":
                    events = events[:2] + [{"artifactUpdate": {"taskId": task_id, "contextId": context,
                        "artifact": {"artifactId": "answer", "parts": [{"text": "unsupported replacement"}]},
                        "append": False}}]
                # recover mode closes after the initial Task; GetTask supplies the final snapshot.
                frames = [("data:" + json.dumps(rpc_envelope(self, event), ensure_ascii=False) + "\n\n").encode()
                          for event in events]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                if mode == "wait":
                    self.send_header("Connection", "close")
                    self.close_connection = True
                else:
                    self.send_header("Content-Length", str(sum(map(len, frames))))
                self.end_headers()
                for event, frame in zip(events, frames):
                    if mode == "queued" and "statusUpdate" in event:
                        assert peer.release_terminal.wait(30), "Queued test never released its first terminal"
                        with peer.lock:
                            peer.tasks[task_id]["status"]["state"] = "TASK_STATE_COMPLETED"
                    self.wfile.write(frame)
                    self.wfile.flush()
                    time.sleep(0.02)
                if mode == "wait":
                    assert peer.stops[task_id].wait(30), "Active task was not canceled"

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.card = {
                     "name": "A2A public E2E", "description": "Initial description", "version": "1",
                     "supportedInterfaces": [{"url": self.base + "/a2a", "protocolBinding": self.binding,
                                              "protocolVersion": "1.0"}],
                     "capabilities": {"streaming": True, "extensions": [{"uri": TOOLS, "required": False}]}, "defaultInputModes": ["text/plain"],
                     "defaultOutputModes": ["text/plain"], "skills": [{"id": "e2e", "name": "Query",
                     "description": "Full skill description", "tags": ["e2e"], "futureField": {"keep": True}}]}

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "A2A peer failed to stop"


def visible_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(visible_text(item) for item in value)
    if isinstance(value, dict):
        return visible_text(value.get("text", value.get("content", "")))
    return ""


class A2aStory(Story):
    def __init__(self, base, owner, peer):
        super().__init__(base, owner)
        self.peer = peer
        self.original_policy = None
        self.policy_revision = None

    def run(self):
        self.request("POST", "/me/ensure-human")
        provider_name = "a2a-e2e-" + uuid.uuid4().hex
        provider = self.request("POST", "/providers", {"slug": provider_name,
            "name": "A2A E2E " + uuid.uuid4().hex,
            "webhook_url": self.peer.base + "/legacy-webhook-must-not-be-used",
            "auth": {"mode": "static_bearer"}, "protocol_version": "2.0"})
        self.peer.bearer = provider["bcs_to_provider_token"]
        admin = provider["provider_admin_token"]
        prefix = "/providers/" + provider["provider_id"] + "/bots"
        registered = self.request("POST", "/a2a/agents", {"provider_bot_ref": "story-" + uuid.uuid4().hex, "owners": [self.owner],
            "agent_card_url": self.peer.base + "/agent-card.json"}, token=admin, status=201)
        assert set(registered) == {"provider_name", "provider_bot_ref", "agent_name", "bot_id", "agent_card_url", "agent_card"}
        assert registered["agent_name"] == self.peer.card["name"]
        bot_id = registered["bot_id"]
        bindings = self.request("GET", prefix, token=admin)
        bot = next(item for item in bindings["items"] if item["bot_uuid"] == bot_id)
        self.bots.append((provider, bot))
        control = prefix + "/" + bot_id + "/a2a"
        record = self.request("GET", control, token=admin)
        assert record["agent_card_url"] == self.peer.base + "/agent-card.json"
        assert record["revision"] == 1
        card_path = "/a2a/agents/" + bot_id + "/agent-card"
        named = self.request("GET", card_path, token=admin)
        assert named["agent_card"] == record["agent_card"]
        assert not ({"provider_id", "bot_uuid", "revision"} & named.keys())
        self.request("GET", card_path, token="wrong-admin", status=401)
        self.request("GET", control, status=401)
        card = self.request("GET", "/bots/" + bot_id + "/agent-card")
        assert card["skills"][0]["description"] == "Full skill description"
        assert card["skills"][0]["futureField"] == {"keep": True}
        assert not self.peer.requests, "Registration must not submit a probe task"
        named_refresh = self.request("POST", card_path + ":refresh", {}, token=admin)
        assert named_refresh["agent_card"] == record["agent_card"]
        record = self.request("GET", control, token=admin)
        unchanged = self.request("POST", control + "/card:refresh", {}, token=admin)
        assert unchanged == record, "Unchanged refresh must preserve the admin revision"
        with self.peer.lock:
            self.peer.card["description"] = "Refreshed description"
        record = self.request("POST", control + "/card:refresh", {}, token=admin)
        assert record["agent_card"]["description"] == "Refreshed description"
        revision = record["revision"]
        inline = copy.deepcopy(record["agent_card"])
        inline["description"] = "Inline replacement"
        named = self.request("PUT", card_path, {"agent_card": inline}, token=admin)
        assert named["agent_card"] == inline and named["agent_card_url"] is None
        record = self.request("GET", control, token=admin)
        assert record["agent_card_url"] is None
        missing_url = self.request("POST", card_path + ":refresh", {}, token=admin, status=409)
        assert missing_url["error"]["code"] == "CARD_URL_REQUIRED"
        assert record["revision"] == revision + 1
        self.request("PUT", control, {"agent_card": inline, "expected_revision": revision}, token=admin, status=409)
        self.request("POST", control + "/card:refresh", {}, token=admin, status=400)
        assert self.request("GET", "/bots/" + bot_id + "/agent-card")["description"] == "Inline replacement"
        with self.peer.lock:
            reads = self.peer.card_reads
        alias = "Billing-" + uuid.uuid4().hex
        inline["name"] = alias
        inline_bot = self.request("POST", prefix, {"provider_bot_ref": "inline", "owners": [self.owner],
                                  "agent_card": inline}, token=admin)
        self.bots.append((provider, inline_bot))
        assert self.peer.card_reads == reads, "Inline registration must not fetch a Card"
        search = self.request("GET", "/bots/search?" + urlencode({"q": alias.lower(), "limit": 1}))
        assert search["total"] == 1 and search["items"][0]["bot_uuid"] == inline_bot["bot_uuid"]
        assert search["items"][0]["agent_card"] == inline
        for skill, expected in [("e2e", 1), ("absent-legacy-skill", 0)]:
            found = self.request("GET", "/bots/discover?" + urlencode({"q": alias.lower(), "skill": skill}))
            assert len(found["bots"]) == expected, f"Alias/Card skill {skill}: expected {expected}, received {len(found['bots'])}"
            if expected:
                assert found["bots"][0]["bot_uuid"] == inline_bot["bot_uuid"]
                assert found["bots"][0]["agent_card"] == inline
        assert not self.peer.requests, "Card administration must not submit work"

        policy = self.request("GET", "/admin/message-delivery/policy")
        self.original_policy = copy.deepcopy(policy["policy"])
        updated = copy.deepcopy(self.original_policy)
        updated["flow_enabled"].update(group=True, system=True)
        updated["bots"][bot_id] = {"mode": "enforce", "max_running": 1,
                                  "min_send_interval_ms": 1, "max_queued": 10}
        applied = self.request("PUT", "/admin/message-delivery/policy", {
            "expected_version": policy["version"], "policy": updated})
        self.policy_revision = applied["version"]
        group = self.group(bot_id, [bot_id, "human_" + self.owner])
        session = self.request("POST", "/groups/" + group + "/sessions", {}, status=201)["session_id"]
        for sequence, mode in enumerate(["stream", "stream", "message", "recover", "tools", "wait",
                                         "stream", "replacement", "stream"], start=1):
            before = self.history(session)
            before_ids = {message["id"] for message in before}
            with self.peer.lock:
                self.peer.mode = mode
            self.request("POST", "/sessions/" + session + "/chat", {"message": "A2A question " + str(sequence)})
            if mode in ("wait", "replacement"):
                task_id = "peer-task-" + str(sequence)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    with self.peer.lock:
                        admitted = task_id in self.peer.tasks
                    if admitted:
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("A2A task was not submitted")
                if mode == "wait":
                    # Visible output proves that BCS saved the initial Task identity.
                    while time.monotonic() < deadline:
                        if any(visible_text(message.get("content", "")) == "A2A-WAITING-" + str(sequence)
                               for message in self.history(session)):
                            break
                        time.sleep(0.05)
                    else:
                        raise AssertionError("Active Task identity was not ingested")
                    result = abort_task(self.base, self.owner, group, session, bot_id)
                    assert result["payload"]["aborted"], "Active A2A Run was not aborted"
                    self.checks += 2
                while time.monotonic() < deadline:
                    with self.peer.lock:
                        canceled = task_id in self.peer.cancels
                    if canceled:
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("Exact remote Task was not canceled: " + mode)
                # The next real chat.send verifies that confirmed cleanup frees the queue slot.
                continue
            expected = "A2A-ANSWER-" + str(sequence)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                messages = self.history(session)
                fresh = [message for message in messages if message["id"] not in before_ids]
                count = sum(expected == visible_text(message.get("content", "")) for message in fresh)
                if mode == "tools":
                    # Tool boundaries split text into separate history segments.
                    text = "".join(visible_text(message.get("content", "")) for message in sorted(fresh, key=lambda row: row["timestamp"])
                                   if message.get("role") == "assistant")
                    count = int(text == expected)
                if count:
                    if mode == "tools":
                        tools = [message for message in fresh if message.get("role") == "tool_result"]
                        assert len(tools) == 1, "Equal tool observations were duplicated"
                        assert tools[0]["metadata"]["tool_call_id"] == "e2e-call-5"
                        assert tools[0]["content"] == "fixture-tool-result"
                        self.checks += 3
                    assert count == 1, "A2A final text was duplicated"
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("A2A output did not reach visible history: " + mode)
        self.queued_rounds(session)
        self.message_context_rounds(group)
        assert len(self.peer.requests) == 13, "Recovery must not resubmit a message"
        assert self.peer.legacy_requests == 0, "A2A Bot used the legacy Provider webhook"
        assert len({request["message"]["messageId"] for request in self.peer.requests}) == 13
        assert all(request["message"].get("contextId") == "peer-context" for request in self.peer.requests[1:11])
        assert "contextId" not in self.peer.requests[11]["message"]
        assert self.peer.requests[12]["message"]["contextId"] == "peer-context"
        with self.peer.lock:
            cancels = list(self.peer.cancels)
        assert cancels == ["peer-task-6", "peer-task-8"], (
            f"Unexpected cancellations ({self.peer.binding}): expected tasks 6 and 8, received {cancels}"
        )
        assert all(request["message"]["messageId"] not in self.peer.cancels for request in self.peer.requests)
        poll_card = copy.deepcopy(record["agent_card"])
        poll_card["capabilities"]["streaming"] = False
        self.request("PUT", control, {"agent_card": poll_card}, token=admin)
        self.poll_rounds(group, bot_id)
        self.named_rounds(provider, provider_name, poll_card)
        exercise_management(self, provider, poll_card)
        print(f"A2A story: {self.checks} live boundary assertions, thirteen stream and five polling deliveries passed")

    def named_rounds(self, provider, provider_name, card):
        card = copy.deepcopy(card)
        card["name"] = "订单助手-" + uuid.uuid4().hex[:8]
        reference = "story-" + uuid.uuid4().hex
        registration_body = {"provider_bot_ref": reference, "owners": [self.owner], "agent_card": card}
        admin = provider["provider_admin_token"]
        registration = self.request("POST", "/a2a/providers/" + provider_name + "/bots",
            registration_body, token=admin, status=201)
        assert registration["bot_name"] == card["name"]
        assert registration["provider_bot_ref"] == reference
        assert not ({"provider_id", "bot_uuid"} & registration.keys())
        bot_id = registration["bot_id"]
        bindings = self.request("GET", "/providers/" + provider["provider_id"] + "/bots", token=provider["provider_admin_token"])
        self.bots.append((provider, next(item for item in bindings["items"] if item["bot_uuid"] == bot_id)))
        # Production A2A requires the server queue for this Bot, including direct calls.
        policy = self.request("GET", "/admin/message-delivery/policy")
        updated = copy.deepcopy(policy["policy"])
        updated["flow_enabled"]["direct_a2a"] = True
        updated["bots"][bot_id] = {"mode": "enforce", "max_running": 1,
                                  "min_send_interval_ms": 1, "max_queued": 10}
        applied = self.request("PUT", "/admin/message-delivery/policy", {
            "expected_version": policy["version"], "policy": updated})
        self.policy_revision = applied["version"]
        token = registration["bot_runtime_token"]
        path = "/a2a/providers/" + provider_name + "/bots/" + quote(reference, safe="") + "/chat-async"
        for sequence, mode in [(17, "poll_task"), (18, "poll_wait")]:
            with self.peer.lock:
                self.peer.mode = mode
            accepted = self.request("POST", path, {"message": "A2A question " + str(sequence)}, token=token, status=202)
            assert accepted["provider_bot_ref"] == reference and "bot_uuid" not in accepted
            run_path = "/a2a/chat/runs/" + accepted["run_id"]
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                result = self.request("GET", run_path + "?wait_ms=500", token=token)
                assert not ({"bot_uuid", "provider_id", "from_bot_id", "provider_bot_ref"} & result.keys())
                if mode == "poll_task" and result.get("is_terminal"):
                    assert result["state"] == "completed"
                    assert result["response"]["content"] == "A2A-ANSWER-17"
                    break
                if mode == "poll_wait":
                    if result.get("is_terminal"):
                        assert result["state"] == "cancelled", result
                        break
                    with self.peer.lock:
                        admitted = "peer-task-18" in self.peer.observed_task_queries
                    if admitted:
                        canceled = self.request("POST", run_path + "/cancel", {}, token=token)
                        if canceled["cancelled"]:
                            break
                time.sleep(0.1)
            else:
                raise AssertionError(f"Name-based run {sequence} failed to complete/cancel: {result}; remote tasks={list(self.peer.tasks)}")
        # BCN cancellation admission precedes the outbound CancelTask delivery.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self.peer.lock:
                if "peer-task-18" in self.peer.cancels:
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("Named cancellation never reached the remote Task")

    def poll_rounds(self, group, bot_id):
        session = self.request("POST", "/groups/" + group + "/sessions", {}, status=201)["session_id"]
        for sequence, mode in [(14, "poll_message"), (15, "poll_task"), (16, "poll_wait")]:
            with self.peer.lock:
                self.peer.mode = mode
            self.request("POST", "/sessions/" + session + "/chat", {"message": "A2A question " + str(sequence)})
            deadline = time.monotonic() + 20
            task_id = "peer-task-" + str(sequence)
            while time.monotonic() < deadline:
                if mode == "poll_wait":
                    with self.peer.lock:
                        # A remote Task can exist before BCS receives SendMessage's
                        # response. Its first GetTask proves identity was saved.
                        admitted = task_id in self.peer.observed_task_queries
                    if admitted:
                        result = abort_task(self.base, self.owner, group, session, bot_id)
                        if result["payload"]["aborted"]:
                            break
                elif any(visible_text(message.get("content", "")) == "A2A-ANSWER-" + str(sequence)
                         for message in self.history(session)):
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("Non-streaming task did not complete/cancel: " + mode)
        with self.peer.lock:
            assert self.peer.methods[13:] == ["/a2a/message:send"] * 3
            assert self.peer.poll_queries["peer-task-15"] >= 2
            assert self.peer.cancels[-1] == "peer-task-16"
            assert len(self.peer.requests) == 16, "Polling must not resubmit"
        self.checks += 4

    def message_context_rounds(self, group):
        session = self.request("POST", "/groups/" + group + "/sessions", {}, status=201)["session_id"]
        with self.peer.lock:
            self.peer.mode = "message"
        for sequence in (12, 13):
            self.request("POST", "/sessions/" + session + "/chat", {"message": "A2A question " + str(sequence)})
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                count = sum(visible_text(message.get("content", "")) == "A2A-ANSWER-" + str(sequence)
                            for message in self.history(session))
                if count == 1:
                    break
                assert count == 0, "Message-only text was duplicated"
                time.sleep(0.1)
            else:
                raise AssertionError("Message-only continuation did not reach visible history")

    def queued_rounds(self, session):
        with self.peer.lock:
            self.peer.mode = "queued"
        self.request("POST", "/sessions/" + session + "/chat", {"message": "A2A question 10"})
        deadline = time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                with self.peer.lock:
                    started = len(self.peer.requests) == 10
                if started:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("First queued request did not reach the peer")
            # The peer holds the first Task open until the second chat is admitted.
            self.request("POST", "/sessions/" + session + "/chat", {"message": "A2A question 11"})
            with self.peer.lock:
                assert len(self.peer.requests) == 10, "Second Send bypassed the occupied queue lane"
                self.checks += 1
        finally:
            self.peer.release_terminal.set()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            messages = self.history(session)
            counts = [sum(visible_text(message.get("content", "")) == "A2A-ANSWER-" + str(sequence)
                          for message in messages) for sequence in (10, 11)]
            if counts == [1, 1]:
                return
            assert all(count <= 1 for count in counts), "Queued terminal text was duplicated"
            time.sleep(0.1)
        raise AssertionError("Queued Send did not progress after the first Task completed")

    def history(self, session):
        history = self.request("GET", "/sessions/" + session + "/messages?limit=100")
        return history if isinstance(history, list) else history.get("messages", [])

    def cleanup(self):
        try:
            super().cleanup()
        finally:
            if self.policy_revision is not None:
                self.request("PUT", "/admin/message-delivery/policy", {
                    "expected_version": self.policy_revision, "policy": self.original_policy})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    for binding in ("HTTP+JSON", "JSONRPC"):
        peer = Peer(binding)
        peer.start()
        story = A2aStory(args.base_url, args.owner, peer)
        try:
            story.run()
            with story.http.open(args.base_url + "/metrics", timeout=15) as response:
                metrics = response.read().decode()
            assert "bcs_http_requests_total{" in metrics
            assert "bcs_message_delivery_total{" in metrics
            if binding == "JSONRPC":
                assert peer.rpc_methods == {"SendMessage", "SendStreamingMessage", "GetTask", "CancelTask", "SubscribeToTask"}
        finally:
            try:
                story.cleanup()
            finally:
                peer.close()
