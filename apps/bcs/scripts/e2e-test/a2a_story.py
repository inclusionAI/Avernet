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
from urllib.parse import unquote, urlsplit

from provider_scope_story import Story


class Peer:
    def __init__(self):
        self.bearer = ""
        self.requests = []
        self.tasks = {}
        self.card_reads = 0
        self.legacy_requests = 0
        self.mode = "stream"
        self.lock = threading.Lock()
        peer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass  # HTTP credentials and protocol messages are never logged.

            def json_response(self, status, body):
                encoded = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def authenticated(self):
                valid = self.headers.get("Authorization") == "Bearer " + peer.bearer
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
                        task = copy.deepcopy(peer.tasks.get(unquote(path.rsplit("/", 1)[-1])))
                    self.json_response(200 if task else 404, task or {})
                else:
                    self.json_response(404, {})

            def do_POST(self):
                path = urlsplit(self.path).path
                if path != "/a2a/message:stream":
                    with peer.lock:
                        peer.legacy_requests += 1
                    self.json_response(404, {})
                    return
                if not self.authenticated():
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with peer.lock:
                    # Group/session lifecycle notifications also use the real A2A link.
                    # Acknowledge them separately from the four human request assertions.
                    question = any("A2A question " in part.get("text", "")
                                   for part in body["message"].get("parts", []))
                    if question:
                        peer.requests.append(body)
                    sequence = len(peer.requests)
                    context = body["message"].get("contextId") or "peer-context"
                    task_id = "peer-task-" + str(sequence)
                    text = "A2A-ANSWER-" + str(sequence)
                    task = {"id": task_id, "contextId": context,
                            "status": {"state": "TASK_STATE_COMPLETED"},
                            "artifacts": [{"artifactId": "answer", "parts": [{"text": text}]}]}
                    peer.tasks[task_id] = task
                    mode = peer.mode if question else "message"
                    if not question:
                        text = "A2A setup acknowledged"
                initial = {"task": {"id": task_id, "contextId": context,
                                    "status": {"state": "TASK_STATE_SUBMITTED"}}}
                events = [initial]
                if mode == "message":
                    events = [{"message": {"messageId": "peer-message-" + str(sequence),
                               "contextId": context, "role": "ROLE_AGENT", "parts": [{"text": text}]}}]
                elif mode == "stream":
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
                # recover mode closes after the initial Task; GetTask supplies the final snapshot.
                frames = [("data:" + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
                          for event in events]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(sum(map(len, frames))))
                self.end_headers()
                for frame in frames:
                    self.wfile.write(frame)
                    self.wfile.flush()
                    time.sleep(0.02)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.card = {"name": "A2A public E2E", "description": "Initial description", "version": "1",
                     "supportedInterfaces": [{"url": self.base + "/a2a", "protocolBinding": "HTTP+JSON",
                                              "protocolVersion": "1.0"}],
                     "capabilities": {"streaming": True}, "defaultInputModes": ["text/plain"],
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
        provider = self.request("POST", "/providers", {
            "name": "A2A E2E " + uuid.uuid4().hex,
            "webhook_url": self.peer.base + "/legacy-webhook-must-not-be-used",
            "auth": {"mode": "static_bearer"}, "protocol_version": "2.0"})
        self.peer.bearer = provider["bcs_to_provider_token"]
        admin = provider["provider_admin_token"]
        prefix = "/providers/" + provider["provider_id"] + "/bots"
        bot = self.request("POST", prefix, {"provider_bot_ref": "remote", "owners": [self.owner],
                           "agent_card_url": self.peer.base + "/agent-card.json"}, token=admin)
        self.bots.append((provider, bot))
        bot_id = bot["bot_uuid"]
        control = prefix + "/" + bot_id + "/a2a"
        record = self.request("GET", control, token=admin)
        assert record["agent_card_url"] == self.peer.base + "/agent-card.json"
        assert record["revision"] == 1
        self.request("GET", control, status=401)
        card = self.request("GET", "/bots/" + bot_id + "/agent-card")
        assert card["skills"][0]["description"] == "Full skill description"
        assert card["skills"][0]["futureField"] == {"keep": True}
        assert not self.peer.requests, "Registration must not submit a probe task"
        unchanged = self.request("POST", control + "/card:refresh", {}, token=admin)
        assert unchanged == record, "Unchanged refresh must preserve the admin revision"
        with self.peer.lock:
            self.peer.card["description"] = "Refreshed description"
        record = self.request("POST", control + "/card:refresh", {}, token=admin)
        assert record["agent_card"]["description"] == "Refreshed description"
        revision = record["revision"]
        inline = copy.deepcopy(record["agent_card"])
        inline["description"] = "Inline replacement"
        record = self.request("PUT", control, {"agent_card": inline, "expected_revision": revision}, token=admin)
        assert record["agent_card_url"] is None
        assert record["revision"] == revision + 1
        self.request("PUT", control, {"agent_card": inline, "expected_revision": revision}, token=admin, status=409)
        self.request("POST", control + "/card:refresh", {}, token=admin, status=400)
        assert self.request("GET", "/bots/" + bot_id + "/agent-card")["description"] == "Inline replacement"
        with self.peer.lock:
            reads = self.peer.card_reads
        inline_bot = self.request("POST", prefix, {"provider_bot_ref": "inline", "owners": [self.owner],
                                  "agent_card": inline}, token=admin)
        self.bots.append((provider, inline_bot))
        assert self.peer.card_reads == reads, "Inline registration must not fetch a Card"
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
        for sequence, mode in enumerate(["stream", "stream", "message", "recover"], start=1):
            with self.peer.lock:
                self.peer.mode = mode
            self.request("POST", "/sessions/" + session + "/chat", {"message": "A2A question " + str(sequence)})
            expected = "A2A-ANSWER-" + str(sequence)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                history = self.request("GET", "/sessions/" + session + "/messages?limit=100")
                messages = history if isinstance(history, list) else history.get("messages", [])
                count = sum(expected == visible_text(message.get("content", "")) for message in messages)
                if count:
                    assert count == 1, "A2A final text was duplicated"
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("A2A output did not reach visible history: " + mode)
        assert len(self.peer.requests) == 4, "Recovery must not resubmit a message"
        assert self.peer.legacy_requests == 0, "A2A Bot used the legacy Provider webhook"
        assert len({request["message"]["messageId"] for request in self.peer.requests}) == 4
        assert all(request["message"].get("contextId") == "peer-context" for request in self.peer.requests[1:])
        print(f"A2A story: {self.checks} live HTTP assertions and four protocol deliveries passed")

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
    peer = Peer()
    peer.start()
    story = A2aStory(args.base_url, args.owner, peer)
    try:
        story.run()
    finally:
        try:
            story.cleanup()
        finally:
            peer.close()
